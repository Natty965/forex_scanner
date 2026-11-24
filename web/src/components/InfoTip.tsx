import { useId } from "react";

import { GLOSSARY, type GlossaryKey } from "../lib/glossary";

/** A small "?" that explains a term on hover or keyboard focus. */
export function InfoTip({ term, text }: { term?: GlossaryKey; text?: string }) {
  const id = useId();
  const body = text ?? (term ? GLOSSARY[term] : "");
  return (
    <span className="infotip">
      <button type="button" className="infotip-btn" aria-describedby={id} aria-label="What does this mean?">
        ?
      </button>
      <span role="tooltip" id={id} className="infotip-body">
        {body}
      </span>
    </span>
  );
}
