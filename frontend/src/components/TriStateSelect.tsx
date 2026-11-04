import { FormSelect, FormSelectOption } from "@patternfly/react-core";
import { TriState } from "../utils";

interface Props {
  id: string;
  value: TriState;
  onChange: (value: TriState) => void;
  defaultLabel?: string;
  isDisabled?: boolean;
}

/** Default / Yes / No select for nullable booleans (default maps to null). */
export function TriStateSelect({
  id,
  value,
  onChange,
  defaultLabel = "Default",
  isDisabled,
}: Props) {
  return (
    <FormSelect
      id={id}
      value={value}
      isDisabled={isDisabled}
      onChange={(_e, v) => onChange(v as TriState)}
    >
      <FormSelectOption value="default" label={defaultLabel} />
      <FormSelectOption value="yes" label="Yes" />
      <FormSelectOption value="no" label="No" />
    </FormSelect>
  );
}
