from setuptools import setup, find_packages

setup(
    name='cli-anything-barcode-query',
    version='1.0.0',
    description='CLI interface for 怡口 CRM 条码查询系统',
    author='CLI-Anything',
    packages=find_packages(include=['cli_anything.*']),
    install_requires=[
        'click>=8.0.0',
    ],
    entry_points={
        'console_scripts': [
            'cli-anything-barcode-query=cli_anything.barcode_query.barcode_query_cli:cli',
        ],
    },
    python_requires='>=3.10',
)
