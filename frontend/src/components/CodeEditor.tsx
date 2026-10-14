// An editable CodeMirror for SQL queries (J4) and provider code (J5).
import { python } from '@codemirror/lang-python'
import { PostgreSQL, SQLite, sql } from '@codemirror/lang-sql'
import { yaml } from '@codemirror/lang-yaml'
import CodeMirror, { EditorView } from '@uiw/react-codemirror'
import { useEffect, useMemo, useState } from 'react'

import { accessibleHighlight } from './highlight'

export type CodeLanguage = 'sql-postgresql' | 'sql-sqlite' | 'python' | 'yaml' | 'text'

function usePrefersDark(): boolean {
  const query = '(prefers-color-scheme: dark)'
  const [dark, setDark] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setDark(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return dark
}

interface Props {
  id: string
  value: string
  language: CodeLanguage
  label: string
  onChange: (value: string) => void
  describedBy?: string
  invalid?: boolean
  minHeight?: string
}

export function CodeEditor({ id, value, language, label, onChange, describedBy, invalid, minHeight = '8rem' }: Props) {
  const dark = usePrefersDark()
  const extensions = useMemo(() => {
    const lang =
      language === 'python'
        ? [python()]
        : language === 'yaml'
          ? [yaml()]
          : language === 'text'
            ? []
            : [sql({ dialect: language === 'sql-sqlite' ? SQLite : PostgreSQL, upperCaseKeywords: true })]
    const attrs: Record<string, string> = { id, 'aria-label': label, 'aria-multiline': 'true' }
    if (describedBy) attrs['aria-describedby'] = describedBy
    if (invalid) attrs['aria-invalid'] = 'true'
    return [...lang, accessibleHighlight(dark), EditorView.lineWrapping, EditorView.contentAttributes.of(attrs)]
  }, [language, id, label, describedBy, invalid, dark])
  return (
    <div className={`code-editor${invalid ? ' invalid' : ''}`}>
      <CodeMirror value={value} extensions={extensions} theme={dark ? 'dark' : 'light'} onChange={onChange} minHeight={minHeight} basicSetup={{ foldGutter: false }} />
    </div>
  )
}
