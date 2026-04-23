# Standalone CMakeLists to build test_qlever binary
# Must be added to the qlever build tree or configured with qlever as a subdirectory
# Usage: cmake -S . -B build -DQLEVER_BUILD_DIR=../../vendor/qlever/build
cmake_minimum_required(VERSION 3.27)
project(test_qlever CXX)
