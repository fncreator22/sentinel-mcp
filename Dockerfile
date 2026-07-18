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

# Expose FastAPI port (8000) and dashboard port (8080)
EXPOSE 8000
EXPOSE 8080

# Command to run both the API and serve the dashboard using a startup shell script
CMD python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 & cd dashboard && python -m http.server 8080
