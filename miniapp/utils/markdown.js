function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(text) {
  let out = escapeHtml(text);

  out = out.replace(/`([^`]+)`/g, '<code style="background:#f3f4f6;padding:2px 6px;border-radius:6px;font-family:Menlo,monospace;font-size:12px;">$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" style="color:#2563eb;text-decoration:underline;">$1</a>');

  return out;
}

function closeLists(state, html) {
  if (state.inUl) {
    html.push("</ul>");
    state.inUl = false;
  }
  if (state.inOl) {
    html.push("</ol>");
    state.inOl = false;
  }
}

function markdownToRichHtml(markdown) {
  const lines = String(markdown || "").replace(/\r/g, "").split("\n");
  const html = [];
  const state = { inUl: false, inOl: false, inCode: false, codeLines: [] };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] || "";

    if (state.inCode) {
      if (/^```/.test(line.trim())) {
        const code = escapeHtml(state.codeLines.join("\n"));
        html.push(
          '<pre style="background:#0f172a;color:#e5e7eb;padding:12px;border-radius:10px;overflow:auto;font-family:Menlo,monospace;font-size:12px;line-height:1.6;margin:8px 0;white-space:pre-wrap;">' +
            code +
            "</pre>"
        );
        state.inCode = false;
        state.codeLines = [];
      } else {
        state.codeLines.push(line);
      }
      continue;
    }

    if (/^```/.test(line.trim())) {
      closeLists(state, html);
      state.inCode = true;
      state.codeLines = [];
      continue;
    }

    if (!line.trim()) {
      closeLists(state, html);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeLists(state, html);
      const level = heading[1].length;
      const content = renderInline(heading[2]);
      const size = [22, 20, 18, 16, 15, 14][Math.min(level - 1, 5)];
      html.push(`<h${level} style="font-size:${size}px;line-height:1.5;margin:8px 0 6px 0;color:#111827;font-weight:700;">${content}</h${level}>`);
      continue;
    }

    const ul = line.match(/^[-*]\s+(.+)$/);
    if (ul) {
      if (state.inOl) {
        html.push("</ol>");
        state.inOl = false;
      }
      if (!state.inUl) {
        html.push('<ul style="padding-left:18px;margin:6px 0;">');
        state.inUl = true;
      }
      html.push(`<li style="margin:4px 0;line-height:1.7;color:#111827;">${renderInline(ul[1])}</li>`);
      continue;
    }

    const ol = line.match(/^\d+\.\s+(.+)$/);
    if (ol) {
      if (state.inUl) {
        html.push("</ul>");
        state.inUl = false;
      }
      if (!state.inOl) {
        html.push('<ol style="padding-left:18px;margin:6px 0;">');
        state.inOl = true;
      }
      html.push(`<li style="margin:4px 0;line-height:1.7;color:#111827;">${renderInline(ol[1])}</li>`);
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      closeLists(state, html);
      html.push(
        '<blockquote style="margin:8px 0;padding:6px 10px;border-left:3px solid #d1d5db;color:#4b5563;background:#f9fafb;border-radius:6px;">' +
          renderInline(quote[1]) +
          "</blockquote>"
      );
      continue;
    }

    closeLists(state, html);
    html.push(`<p style="margin:6px 0;line-height:1.75;color:#111827;">${renderInline(line)}</p>`);
  }

  if (state.inCode) {
    const code = escapeHtml(state.codeLines.join("\n"));
    html.push(
      '<pre style="background:#0f172a;color:#e5e7eb;padding:12px;border-radius:10px;overflow:auto;font-family:Menlo,monospace;font-size:12px;line-height:1.6;margin:8px 0;white-space:pre-wrap;">' +
        code +
        "</pre>"
    );
  }
  closeLists(state, html);

  return html.join("");
}

function markdownToRichNodes(markdown) {
  return markdownToRichHtml(markdown);
}

module.exports = {
  markdownToRichNodes,
};
