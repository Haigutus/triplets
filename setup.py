from setuptools import setup, find_packages
import versioneer

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='triplets',
    version=versioneer.get_version().split("+")[0],
    cmdclass=versioneer.get_cmdclass(),
    packages=find_packages(),
    package_data={
        'triplets.export_schema': ['*.json'],  # Globs all JSON files in the export_schema package
    },
    include_package_data=True,  # Still needed for consistency with MANIFEST.in
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='https://github.com/Haigutus/triplets',
    license='MIT',
    author='Kristjan Vilgo',
    author_email='kristjan.vilgo@gmail.com',
    description='Simple tools to load/modify/export XML/RDF data using Pandas DataFrames',
    install_requires=[
        "pandas", "lxml", 'aniso8601',
    ],
    extras_require={
        'full': [
            "pyvis==0.3.1",
            "pyarrow"
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ]
)
