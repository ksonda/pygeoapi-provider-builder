"""The parse check for SQL in specs (ADR-0007).

A spec's query must parse as exactly one statement whose root is a query (a
SELECT or a set operation), and a walk of the whole tree must find no DML, DDL,
commands, locks or ``SELECT … INTO``, and no function from the denylist. The
database then runs the query **as sqlglot regenerates it**, not the text the
steward wrote, so what runs is what was checked: a trick that parses one way
here and another way in the database can't get through.

The check can't stop a privileged role from calling a dangerous function that
isn't listed, so the engine also runs every query in a read-only transaction
with a statement timeout, and the docs ask for a least-privilege role.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

#: sqlglot's dialect names for the spec's ``dialect``.
DIALECTS = {'postgresql': 'postgres', 'sqlite': 'sqlite'}

#: Statement and clause types that write, lock, change settings or run other statements.
FORBIDDEN: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        'Insert', 'Update', 'Delete', 'Merge', 'Create', 'Drop', 'Alter', 'TruncateTable',
        'Command', 'Copy', 'Into', 'Lock', 'Set', 'SetItem', 'Pragma', 'Transaction',
        'Commit', 'Rollback', 'Grant', 'Revoke', 'Use', 'Describe', 'Show', 'Analyze',
        'LoadData', 'Refresh', 'Cache', 'Uncache', 'Kill', 'Execute', 'Declare', 'Attach',
        'Detach', 'Install', 'Summarize',
    )
    if hasattr(exp, name)
)  # fmt: skip

#: Functions that read files, sleep, lock, reach other servers, change settings or run SQL
#: from a string (PostgreSQL, then SQLite).
DENIED_FUNCTIONS = frozenset(
    {
        'pg_read_file', 'pg_read_binary_file', 'pg_stat_file', 'pg_logdir_ls',
        'lo_import', 'lo_export', 'lo_get', 'lo_put', 'lo_from_bytea', 'lo_open', 'lo_create',
        'lo_unlink', 'lo_truncate', 'loread', 'lowrite',
        'set_config', 'pg_reload_conf', 'pg_rotate_logfile', 'pg_terminate_backend',
        'pg_cancel_backend', 'pg_promote', 'pg_switch_wal', 'pg_create_restore_point',
        'pg_backup_start', 'pg_backup_stop', 'pg_start_backup', 'pg_stop_backup', 'pg_notify',
        'nextval', 'setval', 'currval', 'lastval', 'txid_current',
        'query_to_xml', 'query_to_xmlschema', 'query_to_xml_and_xmlschema', 'cursor_to_xml',
        'cursor_to_xmlschema', 'table_to_xml', 'table_to_xmlschema',
        'table_to_xml_and_xmlschema', 'schema_to_xml', 'schema_to_xmlschema',
        'schema_to_xml_and_xmlschema', 'database_to_xml', 'database_to_xmlschema',
        'database_to_xml_and_xmlschema',
        'load_extension', 'readfile', 'writefile', 'edit', 'fts3_tokenizer', 'zipfile',
        'sleep', 'benchmark', 'load_file', 'get_lock',
    }
)  # fmt: skip
DENIED_PREFIXES = (
    'pg_ls_',
    'pg_file_',
    'pg_sleep',
    'pg_advisory',
    'pg_try_advisory',
    'pg_replication_',
    'pg_logical_',
    'pg_drop_replication',
    'pg_create_logical',
    'pg_create_physical',
    'dblink',
    'sqlar_',
)

PLACEHOLDER_MARK = 'PBPARAM_{}_PB'
MARK = re.compile(r'PBPARAM_([A-Za-z_][A-Za-z0-9_]*?)_PB')


class SQLGuardError(ValueError):
    """A query that isn't one plain, read-only query, or that doesn't parse."""


@dataclass(frozen=True)
class Checked:
    """A query that passed, regenerated for one driver.

    ``sql`` is what runs. For PostgreSQL (psycopg2's pyformat), every ``%`` is
    doubled and placeholders are ``%(name)s``; for SQLite they're ``:name``.
    """

    dialect: str
    sql: str
    placeholders: frozenset[str]

    @property
    def display(self) -> str:
        """The regenerated query as a person would write it (for the UI)."""
        if self.dialect != 'postgresql':
            return self.sql
        text = re.sub(r'%\((\w+)\)s', r':\1', self.sql)
        return text.replace('%%', '%')


def function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return str(node.sql_name()).lower()


def denied(name: str) -> bool:
    return name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES)


@functools.lru_cache(maxsize=256)
def check(query: str, dialect: str, allowed: Iterable[str] = ()) -> Checked:
    """Check ``query``; ``allowed`` names the ``:placeholders`` it may use (none by default)."""
    if dialect not in DIALECTS:
        raise SQLGuardError(f'Unknown SQL dialect {dialect!r}')
    allowed = frozenset(allowed)
    try:
        statements = [s for s in sqlglot.parse(query, read=DIALECTS[dialect]) if s is not None]
    except (ParseError, TokenError) as err:
        first = str(err).splitlines()[0]
        raise SQLGuardError(f"The query doesn't parse: {first}") from err
    if not statements:
        raise SQLGuardError('The query is empty.')
    if len(statements) > 1:
        raise SQLGuardError(
            f'Give one SELECT statement, not {len(statements)} statements separated by ";".'
        )
    root = statements[0]
    if isinstance(root, exp.Subquery):
        root = root.unnest()
    if not isinstance(root, exp.Select | exp.SetOperation):
        raise SQLGuardError(f'Only a SELECT query is allowed, not {root.key.upper()}.')
    used: set[str] = set()
    for node in root.walk():
        if isinstance(node, FORBIDDEN):
            raise SQLGuardError(
                f'{node.key.upper()} is not allowed: the query must only read data.'
            )
        if isinstance(node, exp.Func):
            name = function_name(node)
            if denied(name):
                raise SQLGuardError(f'The function {name}() is not allowed in a provider query.')
        if isinstance(node, exp.Placeholder):
            name = str(node.name)
            if not name or name not in allowed:
                hint = (
                    f'Use one of :{", :".join(sorted(allowed))}.'
                    if allowed
                    else 'This query takes no parameters; filters are added by the provider.'
                )
                raise SQLGuardError(f'Unexpected parameter :{name or "?"}. {hint}')
            used.add(name)
        if isinstance(node, exp.Parameter):
            raise SQLGuardError('Parameters such as @name or $1 are not allowed.')
    for node in list(root.find_all(exp.Placeholder)):
        node.replace(exp.Var(this=PLACEHOLDER_MARK.format(node.name)))
    sql = root.sql(dialect=DIALECTS[dialect], comments=False)
    if dialect == 'postgresql':
        sql = MARK.sub(lambda m: f'%({m.group(1)})s', sql.replace('%', '%%'))
    else:
        sql = MARK.sub(lambda m: f':{m.group(1)}', sql)
    return Checked(dialect, sql, frozenset(used))
