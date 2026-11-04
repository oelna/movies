# Pretty-print JSON using the macOS system awk; no Python, jq, or JSON CLI.
# JSON values are produced by SQLite's JSON functions before reaching this
# script. Keep all characters inside strings verbatim, including backslash
# escapes and UTF-8. Indentation is two spaces per level.

function indent(    n) {
	for (n = 0; n < depth; n++)
		printf "  "
}

function fail(message) {
	printf "JSON formatting failed: %s\n", message > "/dev/stderr"
	failed = 1
	exit 1
}

{
	for (i = 1; i <= length($0); i++) {
		c = substr($0, i, 1)

		if (in_string) {
			printf "%s", c
			if (escaped)
				escaped = 0
			else if (c == "\\")
				escaped = 1
			else if (c == "\"")
				in_string = 0
			continue
		}

		# Whitespace outside JSON strings has no meaning.
		if (c == " " || c == "\t" || c == "\r")
			continue

		empty_container = 0
		if (pending_open) {
			if ((c == "}" && stack[depth] == "{") ||
				(c == "]" && stack[depth] == "[")) {
				empty_container = 1
			} else {
				printf "\n"
				indent()
			}
			pending_open = 0
		}

		if (c == "\"") {
			in_string = 1
			printf "%s", c
		} else if (c == "{" || c == "[") {
			printf "%s", c
			depth++
			stack[depth] = c
			pending_open = 1
		} else if (c == "}" || c == "]") {
			if (depth < 1 || (c == "}" && stack[depth] != "{") ||
				(c == "]" && stack[depth] != "["))
				fail("unbalanced container")
			depth--
			if (!empty_container) {
				printf "\n"
				indent()
			}
			printf "%s", c
		} else if (c == ",") {
			printf ",\n"
			indent()
		} else if (c == ":") {
			printf ": "
		} else {
			printf "%s", c
		}
	}
}

END {
	if (failed)
		exit 1
	if (in_string || depth != 0 || pending_open)
		fail("unterminated string or container")
	printf "\n"
}
