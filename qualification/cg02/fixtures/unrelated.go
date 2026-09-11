package probe

// UnrelatedPragmaDocumentation is deliberately not executable SQLite startup
// logic. A qualified rule must not flag mere coexistence of the two strings.
var UnrelatedPragmaDocumentation = []string{
	"PRAGMA journal_mode = WAL",
	"PRAGMA busy_timeout = 5000",
	"PRAGMA foreign_keys = ON",
}
