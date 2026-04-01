# TODO

## parse.py: list values in key=value syntax

Support lists in the `key=value` argument syntax so JSON arrays don't require
switching to full JSON object mode.

Proposed syntax: `foo=[item1 item2]` or `foo=["item 1" "item 2"]`

- Items separated by whitespace and/or optional commas
- Items may be quoted (shell rules) to include spaces
- Optional trailing comma allowed
- Scalar coercion (bool, int) applied to each item
- Examples:
  - `paths=[src/foo.py src/bar.py]`
  - `tags=["hello world", foo, 42]`

## repl.py: mention input syntax in startup message

The startup message should briefly describe the two supported calling syntaxes
(`key=value` pairs and `{…}` JSON object) so a human can immediately see how
to invoke tools without consulting the README.
