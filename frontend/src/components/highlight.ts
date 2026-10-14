// Syntax colours with WCAG AA contrast (4.5:1) on the editor's light and dark
// backgrounds: GitHub's Primer palette. CodeMirror's defaults fail axe's
// color-contrast rule for some tokens.
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { Prec, type Extension } from '@codemirror/state'
import { tags as t } from '@lezer/highlight'

const LIGHT = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword], color: '#8250df' },
  { tag: [t.string, t.special(t.string), t.regexp, t.docString], color: '#0a3069' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.meta], color: '#57606a', fontStyle: 'italic' },
  { tag: [t.number, t.bool, t.null, t.atom], color: '#0550ae' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.variableName), t.className, t.typeName], color: '#953800' },
  { tag: [t.propertyName, t.attributeName], color: '#116329' },
  { tag: [t.invalid], color: '#cf222e' },
])

const DARK = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword], color: '#d2a8ff' },
  { tag: [t.string, t.special(t.string), t.regexp, t.docString], color: '#a5d6ff' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.meta], color: '#9198a1', fontStyle: 'italic' },
  { tag: [t.number, t.bool, t.null, t.atom], color: '#79c0ff' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.variableName), t.className, t.typeName], color: '#ffa657' },
  { tag: [t.propertyName, t.attributeName], color: '#7ee787' },
  { tag: [t.invalid], color: '#ff7b72' },
])

export function accessibleHighlight(dark: boolean): Extension {
  return Prec.highest(syntaxHighlighting(dark ? DARK : LIGHT))
}
