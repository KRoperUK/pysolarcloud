from setuptools import find_packages, setup

# Package name, version and requires-python are declared in pyproject.toml ([project]);
# setup.py only supplies the dynamic dependency fields.
setup(
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    # Ship the py.typed marker (PEP 561) so consumers get the inline type hints.
    package_data={"pysolarcloud": ["py.typed"]},
    install_requires=[
        "aiohttp",
    ],
    extras_require={
        "dev": [
            "pytest",
            "pytest-asyncio",
            "pytest-cov",
            "mypy",
        ],
    },
)