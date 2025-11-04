"""
Setup verification script for Cognee PDF analysis
Checks that all prerequisites are in place before running the analysis
"""

import sys
import subprocess
import importlib.util


def check_package(package_name, import_name=None):
    """Check if a Python package is installed"""
    if import_name is None:
        import_name = package_name
    
    spec = importlib.util.find_spec(import_name)
    return spec is not None


def check_litellm_proxy():
    """Check if LiteLLM proxy is running"""
    try:
        import requests
        response = requests.get("http://localhost:4000/health", timeout=2)
        return response.status_code == 200
    except:
        return False


def check_aws_credentials():
    """Check if AWS credentials are configured via AWS_PROFILE"""
    import os
    
    # Check if AWS_PROFILE is set
    aws_profile = os.environ.get('AWS_PROFILE')
    if not aws_profile:
        return False, None
    
    # Try to use the profile
    try:
        import boto3
        session = boto3.Session(profile_name=aws_profile)
        sts = session.client('sts')
        identity = sts.get_caller_identity()
        return True, aws_profile
    except:
        return False, aws_profile


def main():
    print("🔍 Checking Cognee PDF Analysis Setup\n")
    print("=" * 60)
    
    all_good = True
    
    # Check Python version
    print("\n1. Python Version")
    version = sys.version_info
    if version.major == 3 and version.minor >= 11:
        print(f"   ✅ Python {version.major}.{version.minor}.{version.micro}")
    else:
        print(f"   ❌ Python {version.major}.{version.minor}.{version.micro} (need 3.11+)")
        all_good = False
    
    # Check required packages
    print("\n2. Required Packages")
    packages = [
        ("boto3", "boto3"),
        ("litellm", "litellm"),
        ("cognee", "cognee"),
    ]
    
    for package_name, import_name in packages:
        if check_package(package_name, import_name):
            print(f"   ✅ {package_name}")
        else:
            print(f"   ❌ {package_name} (not installed)")
            all_good = False
    
    # Check LiteLLM proxy
    print("\n3. LiteLLM Proxy")
    if check_litellm_proxy():
        print("   ✅ Running on http://localhost:4000")
    else:
        print("   ❌ Not running or not accessible")
        print("      Start with: litellm --config config.yml")
        all_good = False
    
    # Check AWS credentials
    print("\n4. AWS Credentials (via AWS_PROFILE)")
    creds_ok, profile = check_aws_credentials()
    if creds_ok:
        print(f"   ✅ AWS_PROFILE set to: {profile}")
        print(f"      Profile is valid and working")
    elif profile:
        print(f"   ❌ AWS_PROFILE set to: {profile}")
        print(f"      But profile is not working or invalid")
        print("      Check your AWS configuration")
        all_good = False
    else:
        print("   ❌ AWS_PROFILE environment variable not set")
        print("      Set it with:")
        print("        export AWS_PROFILE=your-profile")
        print("      Or configure AWS SSO:")
        print("        aws sso login --profile your-profile")
        print("        export AWS_PROFILE=your-profile")
        all_good = False
    
    # Check for PDF files
    print("\n5. PDF Files")
    import os
    pdf_files = [f for f in os.listdir("input") if f.endswith(".pdf")]
    if pdf_files:
        print(f"   ✅ Found {len(pdf_files)} PDF file(s):")
        for pdf in pdf_files:
            print(f"      - {pdf}")
    else:
        print("   ❌ No PDF files found in input/ directory")
        all_good = False
    
    # Summary
    print("\n" + "=" * 60)
    if all_good:
        print("✅ All checks passed! Ready to run analysis.")
        print("\nRun the analysis with:")
        print("  python analyze_pdf.py")
    else:
        print("❌ Some checks failed. Please fix the issues above.")
        print("\nTo install missing packages:")
        print("  pip install boto3 'litellm[proxy]' cognee")
    print("=" * 60)
    
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
