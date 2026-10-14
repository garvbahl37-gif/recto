// A handful of line icons, drawn light so they sit at the weight of the type.
import { Moon, Search, Sun } from "lucide-react";

const MAP = { search: Search, moon: Moon, sun: Sun };

export const Icon = ({ name, size = 16, strokeWidth = 1.75, ...rest }) => {
  const C = MAP[name] || Search;
  return <C size={size} strokeWidth={strokeWidth} aria-hidden {...rest} />;
};
