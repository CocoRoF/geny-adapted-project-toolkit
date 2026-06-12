/**
 * Phase K.1 — safely render assistant-side markdown.
 *
 * Used in two places:
 *   - SessionDetail.tsx (archive viewer)
 *   - ChatPanel.tsx EventRow for `text` events with role != user
 *
 * Pipeline: `marked.parse()` → DOMPurify sanitize → `dangerouslySetInnerHTML`.
 *
 *  - Marked handles inline code, code blocks, lists, headings, links,
 *    emphasis. We disable GitHub-flavored tables / strikethrough for
 *    now — the corner cases (table layout, header alignment) aren't
 *    worth it until the agent actually emits them.
 *  - DOMPurify strips every `<script>`, inline event handler
 *    (`onerror=`, `onclick=`), and `javascript:` URLs. The default
 *    config already covers the OWASP top XSS vectors; we add nothing
 *    custom so future audits aren't trying to chase down our overrides.
 *  - Styling uses the project's tailwind tokens directly — we don't
 *    pull in the tailwind typography plugin because it adds a second
 *    set of design tokens that drift from ours.
 *
 * User-side prompts (the right-aligned bubble) intentionally stay
 * plain `<pre>` — preserving the operator's exact input is more
 * important than rendering markdown they probably didn't intend.
 */

import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
// Phase M.9 — load the `github-dark` highlight.js theme; the rules
// scope to `.hljs` + `.hljs-*` spans that `markedHighlight` emits, so
// this import only adds colors for code blocks (no global leakage).
import "highlight.js/styles/github-dark.css";
import { marked } from "marked";
import { markedHighlight } from "marked-highlight";
import { useMemo } from "react";

import { cn } from "@/ui/cn";

// Phase M.9 — syntax highlighting for fenced code blocks. The
// `highlight.js/lib/common` import pulls the ~40 most-used languages
// (TS / JS / Python / Bash / Go / Rust / SQL / JSON / YAML / etc.)
// without ballooning the bundle with the full ~190-language set.
// `markedHighlight` wraps `marked`'s code-block renderer so the
// downstream `<pre><code class="hljs language-…">` carries the
// tokenized spans for the CSS theme to colour.
marked.use(
  markedHighlight({
    langPrefix: "hljs language-",
    highlight(code, lang) {
      const language = lang && hljs.getLanguage(lang) ? lang : "plaintext";
      try {
        return hljs.highlight(code, { language }).value;
      } catch {
        // Highlighter blew up on malformed input — fall back to plain
        // text so the message still renders.
        return code;
      }
    },
  }),
);

interface Props {
  children: string;
  className?: string;
}

// Configure once at module load. The parser is sync (no async hooks
// since we don't render code blocks via prism), so a single
// `marked.parse` call is fine inline.
marked.setOptions({
  // Newlines in source become <br> — important because LLM responses
  // often use literal newlines as soft breaks rather than markdown's
  // double-newline paragraph separator.
  breaks: true,
  // Don't fail loud on imperfect input — we'd rather render *something*
  // than blank the chat message.
  silent: true,
});

// Phase M.6 — DOMPurify hook to harden `<a>` tags:
//   * `target="_blank"` so links open in a new tab. Previously the
//     comment below claimed this but no code ever set it — clicking
//     an assistant-emitted URL navigated the GAPT IDE iframe away
//     from the chat. The user lost their place.
//   * `rel="noopener noreferrer"` because new-tab + `window.opener`
//     access is a classic phishing escalation vector.
//   * Same-origin links (relative paths, `/_gapt/...`) are left
//     alone so internal jumps still target the current tab.
let _hookInstalled = false;
function _installLinkHook(): void {
  if (_hookInstalled) return;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName !== "A") return;
    const href = node.getAttribute("href");
    if (!href) return;
    // Only mark external schemes — relative + same-origin stay
    // current-tab so the operator clicking `/_gapt/api/...` doesn't
    // get a stray new tab. A leading slash + protocol-relative `//`
    // both count as "internal-or-protocol-relative" — leave them.
    const isExternal =
      /^[a-z][a-z\d+\-.]*:/i.test(href) && !href.startsWith(`${window.location.origin}/`);
    if (!isExternal) return;
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  });
  _hookInstalled = true;
}

export function MarkdownText({ children, className }: Props) {
  const html = useMemo(() => {
    _installLinkHook();
    const raw = marked.parse(children) as string;
    return DOMPurify.sanitize(raw, {
      // Don't allow inline styles either — sanitizer's default for
      // `style=` is "allow"; tightening here removes one more XSS
      // vector (CSS-injected exfiltration).
      FORBID_ATTR: ["style"],
      // Allow target + rel on `<a>` (DOMPurify's default schema
      // already permits them, but be explicit since our hook depends
      // on them surviving sanitization).
      ADD_ATTR: ["target", "rel"],
    });
  }, [children]);

  return (
    <div
      // The whole point of this component is sanitised HTML.
      dangerouslySetInnerHTML={{ __html: html }}
      className={cn(
        "text-[13px] leading-relaxed text-fg",
        // Headings — smaller than browser defaults so they fit a chat
        // bubble. Spacing collapses on first/last child so a heading
        // at the start doesn't leave dead space.
        "[&>h1]:mt-2 [&>h1]:mb-1 [&>h1]:text-[16px] [&>h1]:font-semibold",
        "[&>h2]:mt-2 [&>h2]:mb-1 [&>h2]:text-[14.5px] [&>h2]:font-semibold",
        "[&>h3]:mt-2 [&>h3]:mb-1 [&>h3]:text-[13.5px] [&>h3]:font-semibold",
        "[&>h4]:mt-1.5 [&>h4]:mb-1 [&>h4]:text-[13px] [&>h4]:font-semibold",
        // Paragraphs.
        "[&>p]:my-1.5 [&>p:first-child]:mt-0 [&>p:last-child]:mb-0",
        // Lists.
        "[&>ul]:my-1.5 [&>ul]:list-disc [&>ul]:pl-5",
        "[&>ol]:my-1.5 [&>ol]:list-decimal [&>ol]:pl-5",
        "[&_li]:my-0.5",
        // Inline code.
        "[&_code]:rounded [&_code]:bg-bg-subtle [&_code]:px-1 [&_code]:py-0.5",
        "[&_code]:font-mono [&_code]:text-[11.5px] [&_code]:text-fg",
        // Code blocks — `marked` wraps in `<pre><code>`, so reset the
        // inline-code background so it doesn't double-up.
        "[&>pre]:my-2 [&>pre]:overflow-auto [&>pre]:rounded-md",
        "[&>pre]:border [&>pre]:border-border [&>pre]:bg-bg-subtle",
        "[&>pre]:p-2.5",
        "[&>pre>code]:bg-transparent [&>pre>code]:p-0",
        "[&>pre>code]:text-[11.5px] [&>pre>code]:leading-snug",
        // Blockquotes — used by the operator sometimes for emphasis,
        // and by our own `render_markdown` for the user prompt in
        // downloaded transcripts.
        "[&>blockquote]:my-1.5 [&>blockquote]:border-l-2",
        "[&>blockquote]:border-border [&>blockquote]:pl-3",
        "[&>blockquote]:text-fg-muted",
        // Links — explicit accent, target=_blank since assistant
        // links almost always point off-site.
        "[&_a]:text-accent [&_a]:underline",
        // HRs.
        "[&>hr]:my-3 [&>hr]:border-border",
        // Bold / italic stay as browser defaults.
        className,
      )}
    />
  );
}
