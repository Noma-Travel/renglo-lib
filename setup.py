"""
Renglo Library - Core controllers, models, and utilities for Renglo applications
"""

from setuptools import setup, find_packages

setup(
    name="renglo-lib",
    version="1.0.0",
    description="Core Renglo functionality - controllers, models, and utilities",
    author="Renglo Team",
    packages=find_packages(),
    python_requires=">=3.12",
    install_requires=[
        "boto3==1.35.38",
        "botocore==1.35.38",
        "opensearch-py>=2.4.0",
        "PyJWT==2.10.1",
        "Requests==2.32.3",
        "validate_email==1.3",
        "openai==1.109.1",
        "langfuse>=3.0,<5",
    ],
    extras_require={
        "flask": [
            "Flask==3.1.0",
            "Flask-Cognito==1.21",
            "Flask-Caching==2.1.0",
        ],
    },
    include_package_data=True,
    package_data={
        'renglo': [
            'chat/blueprints/*.json',
        ],
    },
)

