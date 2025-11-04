import boto3
import time

# Set up AWS clients
s3 = boto3.client('s3')
textract = boto3.client('textract', region_name='us-east-1')

bucket_name = 'faa-compass-poc-data'
input_prefix = 'raw-data/pdfs/'
output_prefix = 'processed-data/'

# List all PDF files in the S3 input folder
response = s3.list_objects_v2(Bucket=bucket_name, Prefix=input_prefix)
pdf_files = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.pdf')]

for pdf_key in pdf_files:
    print(f" Processing: {pdf_key}")
    doc_name = pdf_key.split('/')[-1].replace('.pdf', '')

    # Start Textract job
    response = textract.start_document_text_detection(
        DocumentLocation={'S3Object': {'Bucket': bucket_name, 'Name': pdf_key}}
    )
    job_id = response['JobId']

    # Wait for job to complete
    print(" Waiting for Textract job to complete...")
    while True:
        result = textract.get_document_text_detection(JobId=job_id)
        if result['JobStatus'] in ['SUCCEEDED', 'FAILED']:
            break
        time.sleep(5)

    if result['JobStatus'] == 'SUCCEEDED':
        print(" Textract job completed. Extracting text...")

        # Extract all pages using NextToken
        lines = []
        while True:
            for block in result['Blocks']:
                if block['BlockType'] == 'LINE':
                    lines.append(block['Text'])
            if 'NextToken' in result:
                result = textract.get_document_text_detection(JobId=job_id, NextToken=result['NextToken'])
            else:
                break

        # Join lines and upload to processed-data/
        text_output = '\n'.join(lines)
        output_key = f'{output_prefix}{doc_name}.txt'
        s3.put_object(Bucket=bucket_name, Key=output_key, Body=text_output.encode('utf-8'))
        print(f" Uploaded extracted text to: s3://{bucket_name}/{output_key}\n")

    else:
        print(f" Textract job failed for: {pdf_key}")
