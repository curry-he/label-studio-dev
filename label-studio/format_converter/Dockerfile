# Use an official Python runtime as a parent image
FROM python:3.10-slim-bookworm

# Set the working directory in the container
WORKDIR /app

# Install system dependencies required by OpenCV and other libraries
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    Pillow \
    numpy \
    opencv-python-headless \
    python-pachyderm \
    label-studio-converter \
    label-studio-sdk \
    lxml \
    xmltodict

# Copy the content of the current directory into the container at /app
COPY . /app/

# Specify the command to run on container start is not needed,
# as Pachyderm will specify the command in the pipeline definition.