//go:build ruleguard

package gorules

import "github.com/quasilyte/go-ruleguard/dsl"

// sqliteBusyTimeoutOrdering intentionally models only the static shape from
// CG01-IOT-SQLITE-STARTUP-LOCK-ORDER. It is not proof that a SQLite runtime is
// lock-sensitive; the held-lock behavior regression remains authoritative.
func sqliteBusyTimeoutOrdering(m dsl.Matcher) {
	m.Match(`for _, $stmt := range []string{$*_, "PRAGMA journal_mode = WAL", $*_, "PRAGMA busy_timeout = 5000", $*_} {
		if _, $err := $db.Exec($stmt); $err != nil { $*_ }
	}`).Report(`CG02_SQLITE_BUSY_TIMEOUT_ORDER`)
}
