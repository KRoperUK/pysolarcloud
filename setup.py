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
        # AES-ECB + RSA for the user-account (app/web) login envelope (user_auth.py, #40).
        "cryptography",
    ],
    extras_require={
        # Lint/type-check tools are pinned so results are reproducible and consistent
        # with the sungrow-hass integration that consumes this library (#38).
        "dev": [
            "pytest",
            "pytest-asyncio",
            "pytest-cov",
            "ruff==0.15.22",
            "mypy==2.3.0",
            # Loads a local .env for live tests (credentials); CI uses repo secrets.
            "python-dotenv",
        ],
    },
)