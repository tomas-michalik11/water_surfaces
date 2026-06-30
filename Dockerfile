# Use a lightweight, official Python runtime
FROM python:3.11-slim

# Prevent Python from writing .pyc files to disk
ENV PYTHONDONTWRITEBYTECODE=1
# Ensure logs are printed directly to the console without buffering
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Copy only the requirements first to cache the pip install step
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your project files
COPY . .

# Set the default command to run your future incremental update script
CMD ["python", "update.py"]
