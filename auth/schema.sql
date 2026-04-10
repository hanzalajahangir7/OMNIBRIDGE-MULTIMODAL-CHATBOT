PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS auth_users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_salt TEXT,
  password_hash TEXT,
  auth_provider TEXT NOT NULL DEFAULT 'local',
  google_sub TEXT UNIQUE,
  display_name TEXT,
  avatar_url TEXT,
  email_verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY,
  session_token TEXT NOT NULL UNIQUE,
  browser_session_id TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guest_usage (
  browser_session_id TEXT PRIMARY KEY,
  messages_used INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  browser_session_id TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  next_path TEXT NOT NULL DEFAULT '/',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS auth_users_email_idx ON auth_users(email);
CREATE INDEX IF NOT EXISTS auth_users_google_sub_idx ON auth_users(google_sub);
CREATE INDEX IF NOT EXISTS auth_sessions_token_idx ON auth_sessions(session_token);
CREATE INDEX IF NOT EXISTS auth_sessions_browser_idx ON auth_sessions(browser_session_id);
