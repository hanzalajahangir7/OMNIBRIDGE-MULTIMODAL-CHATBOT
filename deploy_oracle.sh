#!/bin/bash
# OMNIBRIDGE Multimodal Chatbot - Oracle Cloud Deployment Script
# This script installs Docker, Ollama, and deploys the OMNIBRIDGE stack.

set -e

echo "🚀 Starting OMNIBRIDGE Deployment on Oracle Cloud..."

# 1. Update and install dependencies
echo "📦 Updating system and installing dependencies..."
if [ -f /etc/oracle-release ]; then
    # Oracle Linux
    sudo dnf update -y
    sudo dnf install -y yum-utils
    sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
elif [ -f /etc/lsb-release ]; then
    # Ubuntu
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg lsb-release
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# 2. Start and enable Docker
echo "⚙️ Configuring Docker..."
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER

# 3. Install Ollama
echo "🧠 Installing Ollama..."
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi

# 4. Pull required models
echo "📥 Pulling AI models (this may take a few minutes)..."
sudo ollama pull llama3.2:3b
sudo ollama pull moondream
sudo ollama pull nomic-embed-text

# 5. Prepare Environment
echo "📝 Setting up environment..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
    else
        cat <<EOT > .env
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_TEXT_MODEL=llama3.2:3b
OLLAMA_VISION_MODEL=moondream
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
DB_ENABLED=true
DB_NAME=chatbot
DB_USER=postgres
DB_PASSWORD=password
DB_HOST=postgres
REDIS_URL=redis://redis:6379/0
EOT
    fi
fi

# 6. Deploy with Docker Compose
echo "🚢 Launching OMNIBRIDGE containers..."
sudo docker compose up -d --build

echo "✅ Deployment complete!"
echo "🌐 Your app should be available on port 8080."
echo "⚠️ Note: Make sure to open port 8080 in your Oracle Cloud VCN Security List."
echo "📊 Current Status:"
sudo docker compose ps
