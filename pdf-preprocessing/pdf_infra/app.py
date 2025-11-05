#!/usr/bin/env python3
import os
from aws_cdk import App, Environment
from pdf_infra.pdf_preprocess_stack import PdfPreprocessStack

# user parameters
# ---------------
# stack name and description
stack_id = 'PDFPreprocessStack'
stack_desc = 'infrastructure to pre process PDFs at scale'
# logging level for the lambda functions
log_level = 'INFO'  #log level for the Lambdas. only INFO is implemented atm.

# stacks to deploy
# ----------------
app = App()

# Define environment once
aws_env = Environment(
    account=os.getenv("655276615738"),
    region=os.getenv("us-east-1")
)

# Get environment configuration from context
env_name = app.node.try_get_context('env')
env_config = {}
if env_name:
    environments = app.node.try_get_context('environments')
    if environments and env_name in environments:
        env_config = environments[env_name]
        print(f"Using environment configuration for: {env_name}")
        print(f"Config: {env_config}")
    else:
        print(f"Warning: Environment '{env_name}' not found in context")
else:
    print("No environment specified via -c env=<name>")

infra_stack = PdfPreprocessStack(
    app, 
    construct_id=stack_id,
    env_config=env_config,
    log_level=log_level,
    description=stack_desc,
    add_final_sns=False,
    env=aws_env
)

app.synth()
