from setuptools import find_packages, setup

# Package name, version and requires-python are declared in pyproject.toml ([project]);
# setup.py only supplies the dynamic dependency fields.
setup(
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "aiohttp",
    ],
    extras_require={
        "dev": [
            "pytest",
            "pytest-asyncio",
            "pytest-cov",
        ],
    },
)