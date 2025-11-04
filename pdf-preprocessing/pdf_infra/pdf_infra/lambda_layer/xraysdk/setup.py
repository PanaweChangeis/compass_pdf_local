from setuptools import setup, find_packages

setup(
    name='xraysdk',
    version='1.0.0',
    description='AWS X-Ray SDK Layer for document processing pipeline',
    packages=find_packages(),
    install_requires=[
        'aws-xray-sdk==2.12.1'
    ],
    python_requires='>=3.12'
)
