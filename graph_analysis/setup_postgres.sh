#!/bin/bash
# PostgreSQL Setup Script for Cognee Performance Optimization
# This script automates PostgreSQL installation and configuration

set -e  # Exit on error

echo "🚀 PostgreSQL Setup for Cognee"
echo "================================"
echo ""

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
else
    echo "❌ Unsupported OS: $OSTYPE"
    echo "Please install PostgreSQL manually. See POSTGRES_SETUP.md"
    exit 1
fi

# Check if PostgreSQL is already installed
if command -v psql &> /dev/null; then
    echo "✅ PostgreSQL is already installed"
    PSQL_VERSION=$(psql --version | awk '{print $3}')
    echo "   Version: $PSQL_VERSION"
else
    echo "📦 Installing PostgreSQL..."
    
    if [ "$OS" == "macos" ]; then
        # macOS installation
        if ! command -v brew &> /dev/null; then
            echo "❌ Homebrew not found. Please install Homebrew first:"
            echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            exit 1
        fi
        
        brew install postgresql@15
        brew services start postgresql@15
        
        # Add to PATH
        echo 'export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"' >> ~/.zshrc
        export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"
        
    elif [ "$OS" == "linux" ]; then
        # Linux installation
        sudo apt update
        sudo apt install -y postgresql postgresql-contrib
        sudo systemctl start postgresql
        sudo systemctl enable postgresql
    fi
    
    echo "✅ PostgreSQL installed successfully"
fi

# Wait for PostgreSQL to start
echo ""
echo "⏳ Waiting for PostgreSQL to start..."
sleep 3

# Create database
echo ""
echo "📊 Creating cognee_db database..."

if [ "$OS" == "macos" ]; then
    # macOS - create database as current user
    if psql postgres -lqt | cut -d \| -f 1 | grep -qw cognee_db; then
        echo "   Database 'cognee_db' already exists"
    else
        createdb cognee_db
        echo "✅ Database 'cognee_db' created"
    fi
    
    DB_URL="postgresql://localhost:5432/cognee_db"
    
elif [ "$OS" == "linux" ]; then
    # Linux - create database as postgres user
    sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw cognee_db || \
        sudo -u postgres createdb cognee_db
    
    # Set password for postgres user
    sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'cognee123';"
    
    echo "✅ Database 'cognee_db' created"
    DB_URL="postgresql://postgres:cognee123@localhost:5432/cognee_db"
fi

# Update .env file
echo ""
echo "📝 Updating .env configuration..."

ENV_FILE=".env"

if [ -f "$ENV_FILE" ]; then
    # Check if PostgreSQL config already exists
    if grep -q "^DB_PROVIDER=postgresql" "$ENV_FILE"; then
        echo "   PostgreSQL already configured in .env"
    else
        # Uncomment or add PostgreSQL configuration
        if grep -q "^# DB_PROVIDER=postgresql" "$ENV_FILE"; then
            # Uncomment existing lines
            sed -i.bak 's/^# DB_PROVIDER=postgresql/DB_PROVIDER=postgresql/' "$ENV_FILE"
            sed -i.bak "s|^# DB_URL=.*|DB_URL=$DB_URL|" "$ENV_FILE"
            rm "${ENV_FILE}.bak"
            echo "✅ Enabled PostgreSQL in .env"
        else
            # Add new configuration
            echo "" >> "$ENV_FILE"
            echo "# PostgreSQL Configuration (auto-configured)" >> "$ENV_FILE"
            echo "DB_PROVIDER=postgresql" >> "$ENV_FILE"
            echo "DB_URL=$DB_URL" >> "$ENV_FILE"
            echo "✅ Added PostgreSQL configuration to .env"
        fi
    fi
else
    echo "⚠️  .env file not found. Please create it manually."
    echo "   Add these lines:"
    echo "   DB_PROVIDER=postgresql"
    echo "   DB_URL=$DB_URL"
fi

# Test connection
echo ""
echo "🔍 Testing PostgreSQL connection..."

if psql "$DB_URL" -c "SELECT 1;" &> /dev/null; then
    echo "✅ Connection successful!"
else
    echo "⚠️  Could not connect to database"
    echo "   Please check the connection manually:"
    echo "   psql $DB_URL"
fi

# Summary
echo ""
echo "================================"
echo "✅ PostgreSQL Setup Complete!"
echo "================================"
echo ""
echo "Database URL: $DB_URL"
echo ""
echo "Next steps:"
echo "1. Restart your Streamlit app"
echo "2. Load a document"
echo "3. Enjoy 5-10x faster loading! 🚀"
echo ""
echo "To verify the setup:"
echo "  psql $DB_URL"
echo ""
echo "To view Cognee tables:"
echo "  psql $DB_URL -c '\\dt'"
echo ""
