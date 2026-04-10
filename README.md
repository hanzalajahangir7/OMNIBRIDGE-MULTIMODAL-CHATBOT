# OMNIBRIDGE Multimodal Chatbot

**OMNIBRIDGE** is a premium, high-performance conversational AI platform featuring a dynamic hybrid-routing engine. It merges local private models (Ollama) with intelligent fallback systems, a PostgreSQL/Vector memory backend, and an ultra-modern Neon-Glass UI. Now with integrated Google OAuth and Local authentication.

---

## ✨ Key Features

- **Hybrid AI Engine**: Routes queries intelligently between local models (**Ollama: Llama3.2**) for privacy/efficiency and specialized vision models (**Moondream/Granite**) for imaging.
- **Advanced Document Extraction**: Built-in support for **.docx** and **.pdf** parsing. Text is extracted locally using specialized libraries and processed within a context-optimized window (2500 chars) to prevent CPU hang-ups.
- **Multimodal Uploads**: Seamlessly handles images and documents with automatic summarization and context injection.
- **Persistent Conversational Memory**: Uses a PostgreSQL/`pgvector` database to store user profiles, long-term memory, and chat history. It remembers who you are and how you like to communicate.
- **Premium User Experience**: 
  - **Neon-Glass UI**: Glassmorphism, neon glows, and fluid interactive canvas backgrounds.
  - **Stability & Performance**: Robust error handling for socket timeouts, proxy issues, and memory management.
- **Secure Authentication**: 
  - **Google OAuth**: One-click "Continue with Google" sign-in.
  - **Local Auth**: Traditional email/password accounts with session management.
- **Data Sovereignty**: Auto-generates local, human-readable text chat logs structured by user email or guest ID directly to the `/user_data` directory.

## 🛠 Tech Stack

- **Frontend**: Vite-powered, Vanilla TypeScript (ES6+) with raw CSS for ultimate performance and design flexibility (no heavy frameworks).
- **Backend**: Python 3 with an async-threaded architecture based on `BaseHTTPRequestHandler`.
- **AI Integration**: Native REST connections to [Ollama](https://ollama.ai) (Local AI) and Google Gemini (Optional fallback).
- **Database**: PostgreSQL with `pgvector` for vector embeddings and conversational memory.
- **Document Processing**: `python-docx` for Word files and `PyPDF2` for PDF extraction.

## 🚀 Quickstart

### Prerequisites

1.  **Python 3.12+** (with `pip` and `venv`)
2.  **PostgreSQL** (running at 5432) with **pgvector** extension installed.
3.  **Ollama** installed and running (`ollama serve`). 
    - Ensure models are pulled: `ollama pull llama3.2:3b` and `ollama pull moondream`.

### 1. Configure Environment

Create a `.env` file in the root directory:
```env
# AI Models
OLLAMA_TEXT_MODEL=llama3.2:3b
OLLAMA_VISION_MODEL=moondream
OLLAMA_BASE_URL=http://127.0.0.1:11434

# Auth (Google Cloud Console)
GOOGLE_CLIENT_ID=your_id
GOOGLE_CLIENT_SECRET=your_secret
APP_BASE_URL=http://localhost:3000

# Database
DB_NAME=chatbot
DB_USER=postgres
DB_PASSWORD=your_db_password
DB_HOST=localhost
DB_PORT=5432
```

### 2. Setup Database Schema
```bash
psql -U postgres -d chatbot -h localhost -f database/schema.sql
```

### 3. Start Backend
```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

### 4. Start Frontend
```bash
cd frontend
npm install
npm run dev
```

## 🛡 Stability & Security
- **Guest Limits**: Built-in logic to limit guest messages, encouraging sign-up for long-term use.
- **Fault-Tolerant Proxying**: Fixes for common Vite/Python proxy issues (header ordering, socket hang-up prevention).
- **History Fallbacks**: Graceful handling of unavailable database services ensures the app stays responsive even during maintenance.

---

> **Note**: OMNIBRIDGE is designed for developers and AI enthusiasts who value speed, privacy, and bleeding-edge aesthetics.
