# logstats C++ optimisation (S3): builds liblogstats.so, loaded through ctypes by
# ../python/logstats/fast.py. MIT, this repository.
CXX := g++
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror -shared -fPIC

.PHONY: all clean

all: liblogstats.so

liblogstats.so: logstats.cpp
	$(CXX) $(CXXFLAGS) -o liblogstats.so logstats.cpp

clean:
	rm -f liblogstats.so
