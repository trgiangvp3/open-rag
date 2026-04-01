import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

function sanitize(html: string): string {
  return DOMPurify.sanitize(html)
}

export const badgeColors = ['bg-blue-600', 'bg-sky-600', 'bg-emerald-600', 'bg-amber-600', 'bg-rose-600', 'bg-cyan-600', 'bg-pink-600', 'bg-teal-600']

export function badgeColor(idx: number): string {
  return badgeColors[idx % badgeColors.length]
}

export function renderMd(text: string): string {
  const html = marked.parse(text, { async: false }) as string
  const withBadges = html.replace(/\[(\d+)\]/g, (_m, n) => {
    const c = badgeColors[(parseInt(n) - 1) % badgeColors.length]
    return `<span class="inline-flex items-center justify-center w-5 h-5 text-[10px] ${c} text-white rounded-full font-bold mx-0.5 align-middle shadow-sm">${n}</span>`
  })
  return sanitize(withBadges)
}

export function renderMdPlain(text: string): string {
  return sanitize(marked.parse(text, { async: false }) as string)
}

export function getSection(metadata?: Record<string, unknown>): string {
  return (metadata?.section ?? '') as string
}

export function getFilename(metadata?: Record<string, unknown>): string {
  return (metadata?.filename ?? '') as string
}
