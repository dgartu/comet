use crate::session::SessionCheckpoint;
use rusqlite::{Connection, TransactionBehavior, params};
use std::fs::{self, OpenOptions};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const MANIFEST_VERSION: i64 = 2;
const CHECKPOINT_VERSION: i64 = 1;
const MAX_SESSION_CHECKPOINTS: usize = 200_000;
const CHECKPOINT_EVICTION_TARGET: usize = 180_000;

pub struct SessionCheckpointStore {
    connection: Connection,
    #[cfg(test)]
    committed_merges: usize,
}

impl SessionCheckpointStore {
    pub fn open(local_data_root: &Path) -> Result<Self, &'static str> {
        let root = local_data_root.join("cache").join("session-checkpoints");
        secure_directory(&root)?;
        let index = root.join("index.sqlite3");
        secure_regular_file(&index)?;
        let connection = Connection::open(index).map_err(|_| "session_checkpoint_unavailable")?;
        connection
            .pragma_update(None, "journal_mode", "WAL")
            .map_err(|_| "session_checkpoint_unavailable")?;
        connection
            .pragma_update(None, "synchronous", "FULL")
            .map_err(|_| "session_checkpoint_unavailable")?;
        connection
            .execute_batch(
                "
                CREATE TABLE IF NOT EXISTS session_checkpoints (
                    recreation_key TEXT NOT NULL
                        CHECK (length(recreation_key) = 64 AND recreation_key NOT GLOB '*[^0-9a-f]*'),
                    manifest_version INTEGER NOT NULL CHECK (manifest_version > 0),
                    checkpoint_version INTEGER NOT NULL CHECK (checkpoint_version > 0),
                    posting_index INTEGER NOT NULL CHECK (posting_index >= 0),
                    begin_offset INTEGER NOT NULL CHECK (begin_offset > 0),
                    end_offset INTEGER NOT NULL CHECK (end_offset >= begin_offset),
                    total_size INTEGER NOT NULL CHECK (total_size >= end_offset),
                    last_access_ms INTEGER NOT NULL CHECK (last_access_ms >= 0),
                    PRIMARY KEY (
                        recreation_key, manifest_version, checkpoint_version, posting_index
                    )
                ) STRICT;
                CREATE INDEX IF NOT EXISTS idx_session_checkpoints_lru_v1
                    ON session_checkpoints(last_access_ms, recreation_key, posting_index);
                ",
            )
            .map_err(|_| "session_checkpoint_unavailable")?;
        Ok(Self {
            connection,
            #[cfg(test)]
            committed_merges: 0,
        })
    }

    pub fn load(&mut self, recreation_key: &str) -> Result<Vec<SessionCheckpoint>, &'static str> {
        let checkpoints = self
            .connection
            .prepare(
                "
                SELECT posting_index, begin_offset, end_offset, total_size
                FROM session_checkpoints
                WHERE recreation_key = ?1
                  AND manifest_version = ?2
                  AND checkpoint_version = ?3
                ORDER BY posting_index
                ",
            )
            .and_then(|mut statement| {
                statement
                    .query_map(
                        params![recreation_key, MANIFEST_VERSION, CHECKPOINT_VERSION],
                        |row| {
                            Ok(SessionCheckpoint {
                                posting_index: usize::try_from(row.get::<_, i64>(0)?)
                                    .map_err(|error| conversion_error(0, error))?,
                                begin: u64::try_from(row.get::<_, i64>(1)?)
                                    .map_err(|error| conversion_error(1, error))?,
                                end: u64::try_from(row.get::<_, i64>(2)?)
                                    .map_err(|error| conversion_error(2, error))?,
                                total_size: u64::try_from(row.get::<_, i64>(3)?)
                                    .map_err(|error| conversion_error(3, error))?,
                            })
                        },
                    )?
                    .collect::<Result<Vec<_>, _>>()
            })
            .map_err(|_| "session_checkpoint_unavailable")?;
        if !checkpoints.is_empty() {
            self.connection
                .execute(
                    "
                    UPDATE session_checkpoints
                    SET last_access_ms = ?4
                    WHERE recreation_key = ?1
                      AND manifest_version = ?2
                      AND checkpoint_version = ?3
                    ",
                    params![
                        recreation_key,
                        MANIFEST_VERSION,
                        CHECKPOINT_VERSION,
                        unix_milliseconds()?
                    ],
                )
                .map_err(|_| "session_checkpoint_unavailable")?;
        }
        Ok(checkpoints)
    }

    pub fn merge(
        &mut self,
        recreation_key: &str,
        checkpoints: &[SessionCheckpoint],
    ) -> Result<(), &'static str> {
        if checkpoints.is_empty() {
            return Ok(());
        }
        let now = unix_milliseconds()?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|_| "session_checkpoint_unavailable")?;
        {
            let mut statement = transaction
                .prepare_cached(
                    "
                    INSERT INTO session_checkpoints(
                        recreation_key, manifest_version, checkpoint_version,
                        posting_index, begin_offset, end_offset, total_size, last_access_ms
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
                    ON CONFLICT(
                        recreation_key, manifest_version, checkpoint_version, posting_index
                    ) DO UPDATE SET
                        begin_offset = excluded.begin_offset,
                        end_offset = excluded.end_offset,
                        total_size = excluded.total_size,
                        last_access_ms = excluded.last_access_ms
                    ",
                )
                .map_err(|_| "session_checkpoint_unavailable")?;
            for checkpoint in checkpoints {
                statement
                    .execute(params![
                        recreation_key,
                        MANIFEST_VERSION,
                        CHECKPOINT_VERSION,
                        checkpoint.posting_index as i64,
                        checkpoint.begin as i64,
                        checkpoint.end as i64,
                        checkpoint.total_size as i64,
                        now,
                    ])
                    .map_err(|_| "session_checkpoint_unavailable")?;
            }
        }
        let count = usize::try_from(
            transaction
                .query_row("SELECT count(*) FROM session_checkpoints", [], |row| {
                    row.get::<_, i64>(0)
                })
                .map_err(|_| "session_checkpoint_unavailable")?,
        )
        .map_err(|_| "session_checkpoint_unavailable")?;
        if count > MAX_SESSION_CHECKPOINTS {
            let remove = count - CHECKPOINT_EVICTION_TARGET;
            transaction
                .execute(
                    "
                    DELETE FROM session_checkpoints
                    WHERE rowid IN (
                        SELECT rowid
                        FROM session_checkpoints
                        ORDER BY last_access_ms, recreation_key, posting_index
                        LIMIT ?1
                    )
                    ",
                    params![remove as i64],
                )
                .map_err(|_| "session_checkpoint_unavailable")?;
        }
        transaction
            .commit()
            .map_err(|_| "session_checkpoint_unavailable")?;
        #[cfg(test)]
        {
            self.committed_merges += 1;
        }
        Ok(())
    }

    pub fn discard(&mut self, recreation_key: &str) -> Result<(), &'static str> {
        self.connection
            .execute(
                "DELETE FROM session_checkpoints WHERE recreation_key = ?1",
                params![recreation_key],
            )
            .map(drop)
            .map_err(|_| "session_checkpoint_unavailable")
    }

    #[cfg(test)]
    pub fn committed_merges(&self) -> usize {
        self.committed_merges
    }
}

fn secure_directory(path: &Path) -> Result<(), &'static str> {
    fs::create_dir_all(path).map_err(|_| "session_checkpoint_unavailable")?;
    let metadata = fs::symlink_metadata(path).map_err(|_| "session_checkpoint_unavailable")?;
    if !metadata.file_type().is_dir() {
        return Err("session_checkpoint_unavailable");
    }
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| "session_checkpoint_unavailable")
}

fn secure_regular_file(path: &Path) -> Result<(), &'static str> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|_| "session_checkpoint_unavailable")?;
    let metadata = file
        .metadata()
        .map_err(|_| "session_checkpoint_unavailable")?;
    if !metadata.file_type().is_file() {
        return Err("session_checkpoint_unavailable");
    }
    file.set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|_| "session_checkpoint_unavailable")
}

fn unix_milliseconds() -> Result<i64, &'static str> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "session_checkpoint_unavailable")
        .and_then(|duration| {
            i64::try_from(duration.as_millis()).map_err(|_| "session_checkpoint_unavailable")
        })
}

fn conversion_error(column: usize, error: std::num::TryFromIntError) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        column,
        rusqlite::types::Type::Integer,
        Box::new(error),
    )
}

#[cfg(test)]
mod tests {
    use super::SessionCheckpointStore;
    use crate::session::SessionCheckpoint;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temporary_directory(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "comet-session-checkpoint-{label}-{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn persists_versioned_checkpoints_in_posting_order() {
        let root = temporary_directory("roundtrip");
        let key = "a".repeat(64);
        let checkpoints = [
            SessionCheckpoint {
                posting_index: 2,
                begin: 7,
                end: 9,
                total_size: 9,
            },
            SessionCheckpoint {
                posting_index: 0,
                begin: 1,
                end: 3,
                total_size: 9,
            },
        ];
        {
            let mut store = SessionCheckpointStore::open(&root).unwrap();
            store.merge(&key, &checkpoints).unwrap();
        }
        let mut reopened = SessionCheckpointStore::open(&root).unwrap();
        let loaded = reopened.load(&key).unwrap();

        assert_eq!(loaded, [checkpoints[1], checkpoints[0]]);
        std::fs::remove_dir_all(root).unwrap();
    }
}
