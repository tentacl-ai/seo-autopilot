from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="seo-autopilot",
    version="1.0.2",
    description="Multi-tenant SEO automation platform – real crawler, GSC, PageSpeed, AI agents",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Tentacl",
    author_email="hello@tentacl.ai",
    url="https://tentacl.ai/seo-autopilot/",
    project_urls={
        "Homepage": "https://tentacl.ai/seo-autopilot/",
        "Repository": "https://github.com/tentacl-ai/seo-autopilot",
        "Bug Tracker": "https://github.com/tentacl-ai/seo-autopilot/issues",
        "Documentation": "https://github.com/tentacl-ai/seo-autopilot#readme",
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Internet :: WWW/HTTP :: Indexing/Search",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    packages=find_packages(exclude=["tests*", "docs*", "examples*"]),
    package_data={
        "seo_autopilot": ["reports/templates/*.html"],
    },
    python_requires=">=3.9",
    install_requires=[
        "fastapi>=0.110.0",
        "uvicorn[standard]>=0.27.1",
        "pydantic>=2.6.0",
        "pydantic-settings>=2.1.0",
        "httpx>=0.27.0",
        "python-dotenv>=1.0.0",
        "sqlalchemy>=2.0.0",
        "alembic>=1.13.0",
        "aiosqlite>=0.19.0",
        "apscheduler>=3.10.0",
        "beautifulsoup4>=4.12.0",
        "anthropic>=0.40.0",
        "click>=8.0.0",
        "pyyaml>=6.0",
        "jinja2>=3.1.0",
        "google-api-python-client>=2.100.0",
        "google-auth-oauthlib>=1.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.0",
            "black>=23.0",
            "flake8>=6.0",
            "mypy>=1.0",
        ],
        "postgres": ["asyncpg>=0.29.0", "psycopg2-binary>=2.9.9"],
        "notifications": ["httpx>=0.27.0"],
        "intelligence": ["feedparser>=6.0.0"],
    },
    entry_points={
        "console_scripts": [
            "seo-autopilot=seo_autopilot.cli.main:cli",
        ],
    },
    license="MIT",
    keywords=[
        "seo", "automation", "crawler", "google-search-console",
        "pagespeed", "lighthouse", "multi-tenant", "audit",
    ],
)
