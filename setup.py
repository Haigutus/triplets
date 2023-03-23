from setuptools import setup
import versioneer

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='triplets',
    version=versioneer.get_version().split("+")[0],
    cmdclass=versioneer.get_cmdclass(),
    packages=['triplets'],
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='https://github.com/Haigutus/rdf_tools',
    license='MIT',
    author='Kristjan Vilgo',
    author_email='kristjan.vilgo@gmail.com',
    description='Simple RDF tools to load/modify/export RDF data using Pandas DataFrames',
    install_requires=[
        "pandas", "lxml", 'aniso8601',
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ]
)
