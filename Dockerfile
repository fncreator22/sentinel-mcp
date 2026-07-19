FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Train the classifier during build to ensure model pickles are present and compatible
RUN python train/train_classifier.py

# Expose FastAPI port (8000), dashboard port (8080), and SSE MCP port (8002)
EXPOSE 8000
EXPOSE 8080
EXPOSE 8002

# Command to run the API, SSE MCP server, and serve the dashboard using a startup shell script
# Run API, SSE server, and dashboard in background
CMD python mcp_server/sse_server.py --port 8002 & python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 & cd dashboard && python -m http.server 8080
