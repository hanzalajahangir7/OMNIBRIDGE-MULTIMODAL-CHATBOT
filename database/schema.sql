CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  role TEXT CHECK (role IN ('user', 'assistant')),
  content TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_chunks (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  embedding VECTOR(768),
  importance_score FLOAT DEFAULT 0.5,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_profiles (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  summary TEXT,
  embedding VECTOR(768),
  communication_style TEXT,
  expertise_level TEXT,
  preferred_tone TEXT,
  interests TEXT[] DEFAULT ARRAY[]::TEXT[],
  goals TEXT[] DEFAULT ARRAY[]::TEXT[],
  last_updated TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS embedding VECTOR(768);
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS communication_style TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS expertise_level TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS preferred_tone TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS interests TEXT[] DEFAULT ARRAY[]::TEXT[];
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS goals TEXT[] DEFAULT ARRAY[]::TEXT[];
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS last_updated TIMESTAMP DEFAULT NOW();
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

UPDATE user_profiles
SET last_updated = COALESCE(last_updated, updated_at, NOW())
WHERE last_updated IS NULL;

CREATE INDEX IF NOT EXISTS memory_embedding_idx
ON memory_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE INDEX IF NOT EXISTS profile_embedding_idx
ON user_profiles
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 50);
