module stella-demo-gen

go 1.26.3

require github.com/contre95/stella v0.0.0

// demo/build.sh points this at the stella checkout it builds from.
replace github.com/contre95/stella => ../../.stella
