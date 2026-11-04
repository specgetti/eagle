import { useState } from "react";
import { Chip, ChipGroup, TextInputGroup, TextInputGroupMain, TextInputGroupUtilities } from "@patternfly/react-core";
import { splitList } from "../utils";

interface Props {
  id: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  ariaLabel?: string;
}

/**
 * Chip input: type a value and press Enter or comma to add it. Pasting a comma
 * separated list adds every entry. Duplicates are ignored.
 */
export function ChipListInput({ id, values, onChange, placeholder, ariaLabel }: Props) {
  const [draft, setDraft] = useState("");

  const commit = (text: string) => {
    const add = splitList(text).filter((v) => !values.includes(v));
    if (add.length) onChange([...values, ...Array.from(new Set(add))]);
    setDraft("");
  };

  return (
    <TextInputGroup>
      <TextInputGroupMain
        inputId={id}
        aria-label={ariaLabel}
        value={draft}
        placeholder={values.length ? undefined : placeholder}
        onChange={(_e, v) => {
          if (v.includes(",")) commit(v);
          else setDraft(v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(draft);
          } else if (e.key === "Backspace" && !draft && values.length) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={() => draft && commit(draft)}
      >
        <ChipGroup numChips={20}>
          {values.map((v) => (
            <Chip key={v} onClick={() => onChange(values.filter((x) => x !== v))}>
              {v}
            </Chip>
          ))}
        </ChipGroup>
      </TextInputGroupMain>
      <TextInputGroupUtilities />
    </TextInputGroup>
  );
}
