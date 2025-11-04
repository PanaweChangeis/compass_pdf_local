# Use the AWS Lambda Python runtime as a base image
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies including Ghostscript
RUN mkdir -p /etc/dnf && echo -e "[main]\\nsslverify=0" > /etc/dnf/dnf.conf && dnf update -y && dnf install -y ghostscript && dnf clean all

# Note: PyMuPDF uses built-in fonts, so no system font installation is needed
# This eliminates font-related containerization issues

# Copy the function code and any dependencies files into the container
COPY lambda/selectable_pdf ${LAMBDA_TASK_ROOT}
COPY lambda_layer/pdfprocessor/pdfprocessor ${LAMBDA_TASK_ROOT}/pdfprocessor
COPY lambda_layer/helpertools/helpertools ${LAMBDA_TASK_ROOT}/helpertools
COPY lambda_layer/textracttools/textracttools ${LAMBDA_TASK_ROOT}/textracttools
COPY lambda_layer/xraysdk/xraysdk ${LAMBDA_TASK_ROOT}/xraysdk

# Copy the requirements files from each layer
COPY lambda/selectable_pdf/requirements.txt ${LAMBDA_TASK_ROOT}/selectable_pdf_requirements.txt
COPY lambda_layer/pdfprocessor/requirements.txt ${LAMBDA_TASK_ROOT}/pdfprocessor_requirements.txt
COPY lambda_layer/helpertools/requirements.txt ${LAMBDA_TASK_ROOT}/helpertools_requirements.txt
COPY lambda_layer/textracttools/requirements.txt ${LAMBDA_TASK_ROOT}/textracttools_requirements.txt
COPY lambda_layer/xraysdk/requirements.txt ${LAMBDA_TASK_ROOT}/xraysdk_requirements.txt

# Install all dependencies with trusted hosts to handle SSL issues
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r ./selectable_pdf_requirements.txt
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r ./pdfprocessor_requirements.txt
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r ./helpertools_requirements.txt
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r ./textracttools_requirements.txt
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r ./xraysdk_requirements.txt

# Set Python path for imported modules
ENV PYTHONPATH="${LAMBDA_TASK_ROOT}/helpertools:${LAMBDA_TASK_ROOT}/textracttools:${LAMBDA_TASK_ROOT}/pdfprocessor:${LAMBDA_TASK_ROOT}/xraysdk"

# PyMuPDF built-in fonts are used - no system font configuration needed


# Set the CMD to your handler (e.g., main.lambda_handler)
CMD ["main.lambda_handler"]
