# Run Tool

This run tool will install and run a specific list of tools.

```bash
# Run one of the pre-configured tools
run gdu

# Show path the the tool
run-which gdu

# Install all
for tool in $(run 2>&1 | grep '{' | grep -oE '[0-9a-z\.-]+'); do run-which "${tool}"; done
```
