import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { TextContent } from "@patternfly/react-core";

/** Render review markdown with GFM. Raw HTML is not rendered (no rehype-raw). */
export function Markdown({ children }: { children: string }) {
  return (
    <TextContent className="eagle-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </TextContent>
  );
}
