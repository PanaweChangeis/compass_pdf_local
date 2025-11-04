# Dockerfile for PDF compression Fargate task
# Uses GhostScript for PDF compression without Lambda timeout constraints

FROM public.ecr.aws/lambda/python:3.12

# Install GhostScript and dependencies (Amazon Linux 2023 uses dnf, not yum)
RUN dnf update -y && \
    dnf install -y ghostscript && \
    dnf clean all && \
    rm -rf /var/cache/dnf

# Install Python dependencies
# Note: boto3 is pre-installed in the Lambda base image
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir aws-xray-sdk

# Copy the compression script (relative to build context which is pdf_infra/)
COPY lambda/compress_pdf/main.py /app/main.py

# Set working directory
WORKDIR /app

# Set Python to run in unbuffered mode for better logging
ENV PYTHONUNBUFFERED=1

# Run the compression script
ENTRYPOINT ["python", "main.py"]
