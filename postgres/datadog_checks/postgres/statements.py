# (C) Datadog, Inc. 2020-present
# All rights reserved
# Licensed under Simplified BSD License (see LICENSE)
from __future__ import unicode_literals

import time

import psycopg2
import psycopg2.extras

from datadog_checks.base.log import get_check_logger
from datadog_checks.base.utils.db.sql import compute_sql_signature
from datadog_checks.base.utils.db.statement_metrics import StatementMetrics
from datadog_checks.base.utils.db.utils import default_json_event_encoding, resolve_db_host
from datadog_checks.base.utils.serialization import json

try:
    import datadog_agent
except ImportError:
    from ..stubs import datadog_agent

STATEMENTS_QUERY = """
SELECT {cols}
  FROM {pg_stat_statements_view} as pg_stat_statements
  LEFT JOIN pg_roles
         ON pg_stat_statements.userid = pg_roles.oid
  LEFT JOIN pg_database
         ON pg_stat_statements.dbid = pg_database.oid
  WHERE pg_database.datname = %s
  AND query != '<insufficient privilege>'
  LIMIT {limit}
"""

DEFAULT_STATEMENTS_LIMIT = 10000

# Required columns for the check to run
PG_STAT_STATEMENTS_REQUIRED_COLUMNS = frozenset({'calls', 'query', 'total_time', 'rows'})

PG_STAT_STATEMENTS_METRICS_COLUMNS = frozenset(
    {
        'calls',
        'total_time',
        'rows',
        'shared_blks_hit',
        'shared_blks_read',
        'shared_blks_dirtied',
        'shared_blks_written',
        'local_blks_hit',
        'local_blks_read',
        'local_blks_dirtied',
        'local_blks_written',
        'temp_blks_read',
        'temp_blks_written',
    }
)

PG_STAT_STATEMENTS_TAG_COLUMNS = frozenset(
    {
        'datname',
        'rolname',
        'query',
    }
)

PG_STAT_STATEMENTS_OPTIONAL_COLUMNS = frozenset({'queryid'})

PG_STAT_ALL_DESIRED_COLUMNS = (
    PG_STAT_STATEMENTS_METRICS_COLUMNS | PG_STAT_STATEMENTS_TAG_COLUMNS | PG_STAT_STATEMENTS_OPTIONAL_COLUMNS
)


def _row_keyfunc(row):
    # old versions of pg_stat_statements don't have a query ID so fall back to the query string itself
    queryid = row['queryid'] if 'queryid' in row else row['query']
    return (queryid, row['datname'], row['rolname'])


class PostgresStatementMetrics(object):
    """Collects telemetry for SQL statements"""

    def __init__(self, check, config):
        self._check = check
        self._config = config
        self._db_hostname = resolve_db_host(self._config.host)
        self._log = get_check_logger()
        self._state = StatementMetrics()

    def _execute_query(self, cursor, query, params=()):
        try:
            cursor.execute(query, params)
            return cursor.fetchall()
        except (psycopg2.ProgrammingError, psycopg2.errors.QueryCanceled) as e:
            self._log.warning('Statement-level metrics are unavailable: %s', e)
            return []

    def _get_pg_stat_statements_columns(self, db):
        """
        Load the list of the columns available under the `pg_stat_statements` table. This must be queried because
        version is not a reliable way to determine the available columns on `pg_stat_statements`. The database can
        be upgraded without upgrading extensions, even when the extension is included by default.
        """
        # Querying over '*' with limit 0 allows fetching only the column names from the cursor without data
        query = STATEMENTS_QUERY.format(
            cols='*',
            pg_stat_statements_view=self._config.pg_stat_statements_view,
            limit=0,
        )
        cursor = db.cursor()
        self._execute_query(cursor, query, params=(self._config.dbname,))
        colnames = [desc[0] for desc in cursor.description]
        return colnames

    def collect_per_statement_metrics(self, db, tags):
        try:
            rows = self._collect_metrics_rows(db)
            if not rows:
                return
            payload = {
                'host': self._db_hostname,
                'timestamp': time.time() * 1000,
                'min_collection_interval': self._config.min_collection_interval,
                'tags': tags,
                'postgres_rows': rows,
            }
            self._check.database_monitoring_query_metrics(json.dumps(payload, default=default_json_event_encoding))
        except Exception:
            db.rollback()
            self._log.exception('Unable to collect statement metrics due to an error')
            return []

    def _load_pg_stat_statements(self, db):
        available_columns = set(self._get_pg_stat_statements_columns(db))
        missing_columns = PG_STAT_STATEMENTS_REQUIRED_COLUMNS - available_columns
        if len(missing_columns) > 0:
            self._log.warning(
                'Unable to collect statement metrics because required fields are unavailable: %s',
                ', '.join(list(missing_columns)),
            )
            return []

        # sort to ensure consistent column order
        query_columns = sorted(list(PG_STAT_ALL_DESIRED_COLUMNS & available_columns))

        return self._execute_query(
            db.cursor(cursor_factory=psycopg2.extras.DictCursor),
            STATEMENTS_QUERY.format(
                cols=', '.join(query_columns),
                pg_stat_statements_view=self._config.pg_stat_statements_view,
                limit=DEFAULT_STATEMENTS_LIMIT,
            ),
            params=(self._config.dbname,),
        )

    def _collect_metrics_rows(self, db):
        raw_rows = self._load_pg_stat_statements(db)
        self._check.gauge('dd.postgres.queries.query_rows_raw', len(raw_rows))
        rows = self._state.compute_derivative_rows(raw_rows, PG_STAT_STATEMENTS_METRICS_COLUMNS, key=_row_keyfunc)

        if not rows:
            return None

        # obfuscate & apply signature
        for row in rows:
            try:
                row['query'] = datadog_agent.obfuscate_sql(row['query'])
            except Exception as e:
                # TODO: rate limited error logging & metrics
                # If query obfuscation fails, it is acceptable to log the raw query here because the
                # pg_stat_statements table contains no parameters in the raw queries.
                self._log.warning("Failed to obfuscate query '%s': %s", row['query'], e)
                continue

            if not row['query']:
                continue

            row['query_signature'] = compute_sql_signature(row['query'])

        return rows
