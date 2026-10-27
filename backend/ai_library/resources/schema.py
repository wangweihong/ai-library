def create_resource_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK(kind IN ('templates','skills')),
        data TEXT NOT NULL, search_text TEXT NOT NULL,
        builtin INTEGER NOT NULL DEFAULT 0, preset_id TEXT UNIQUE,
        active INTEGER NOT NULL DEFAULT 1, number INTEGER NOT NULL,
        version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS resource_versions (
        resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        number INTEGER NOT NULL, data TEXT NOT NULL, version TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(resource_id,number))""")
    conn.execute("CREATE TABLE IF NOT EXISTS resource_tag_names (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE)")
    conn.execute("""CREATE TABLE IF NOT EXISTS resource_tags (
        resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES resource_tag_names(id), PRIMARY KEY(resource_id,tag_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS file_blobs (
        hash TEXT PRIMARY KEY, content BLOB NOT NULL, size INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS resource_files (
        resource_id INTEGER NOT NULL, number INTEGER NOT NULL, path TEXT NOT NULL,
        hash TEXT NOT NULL REFERENCES file_blobs(hash),
        PRIMARY KEY(resource_id,number,path),
        FOREIGN KEY(resource_id,number) REFERENCES resource_versions(resource_id,number) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_resources_kind ON resources(kind,active,builtin,updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_tags_tag ON resource_tags(tag_id,resource_id)")
