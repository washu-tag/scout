# Monorepo Build Tools
In a microservice environment, especially a multilanguage microservice environment, we are left with a heterogeneous build / test / deploy process. Some parts of the code base will different build tools than other parts. E.g. gradle for java and whatever for python, probably `setuptools`. 

This document contains notes and discussion of potential tools that could unify the whole build.

## Tools
### Bob
[Bob](https://bob.build/)

Executes arbitrary build task in isolated environments. Built atop [nix](https://nixos.org/) OS and nix packages, which could be something of a barrier to adoption.

Build is defined in one yaml config file.
They have a remote cache in the cloud (public beta, then paid) or locally hosted.

From [Features](https://bob.build/docs/#features):

> Mount build dependencies to your sources instead of sending a build context.

I'm guessing this is in response to docker's build mechanism.
### Earthly
[Earthly](https://earthly.dev/)

Aims to be a be a middle tool between CI (or local) builds and language-specific build systems.

Write builds in a `Dockerfile`-esque syntax. Builds execute in containers.
Uses whatever build tools you want, because they're inside the containers.
### Pants
[Pants](https://www.pantsbuild.org/)
This aims to be a replacement build tool for all the other build tools.
I don't think we want a totally new build tool.
### Please
[Please](https://please.build/)

> cross-language build system with an emphasis on high performance, extensibility and correctness

Can build python and java, but doesn't seem to integrate with gradle. It's compiling the java itself. Does let you specify a JDK url to download, which is nice.

Totally new build tool, replacement for all the other build tools.
### Buck2
[Buck2](https://buck2.build)

From Meta. They also have a Buck1 but it is no longer under active development and has been replaced by Buck2.

Seems like it is still pretty in-dev. Their [Why Use Buck2?](https://buck2.build/docs/about/why/#why-use-buck2) page lists a bunch of stuff that is different between the open-source and internal versions, and other things that aren't yet done.

Also seems optimized for their remote execution context, with builds happening in a multi-user networked infrastructure and being cached there for reuse by others. That isn't what we are doing.
### Bazel
[Bazel](https://bazel.build)

This is a completely alternative build tool. Like, I don't think we would use Gradle anymore, we would just use Bazel. That's a bit more than I think we need at this point.
Also seems like it doesn't manage dependencies for you. You have to load all the dependencies in yourself, as in vendor them into your repo.

- Gradle blog post [Gradle vs Bazel for JVM Projects](https://blog.gradle.org/gradle-vs-bazel-jvm) declares Gradle is better.
- Bob blog post [What makes Bob a great Bazel alternative](https://bob.build/blog/vs-bazel) declares Bob is better.
### Gradle
It does seem to be possible to build python with gradle.  
Uses [linkedin/pygradle](https://github.com/linkedin/pygradle) gradle plugin.

Major limitation: dependencies cannot be pulled directly from PyPI, because they do not have the Ivy metadata that gradle needs. Linkedin supposedly has a bunch of python packages + Ivy metadata up on [their public artifactory](https://linkedin.jfrog.io/linkedin/webapp/#/artifacts/browse/tree/General/pypi-external), but that link is broken at time of writing. The artifactory exists but the `pypi-external` tree does not.
Seems to be dead, actually. No packages, no activity on repo in years.
## Discussion

I did some looking around at different build tools that we could use to manage this situation. What I was looking for is something that provides a standard way to interact with / run whatever build tool we are already using. I don't want to rip out and replace the builds we have. I want to continue using the build tools that we've built up years of experience with, but maybe have a common interface layer in front of them to make calling and executing them more streamlined.

After having looked through several options, I don't really think anything fits exactly. The closest fits to what I was envisioning are Bob and Earthly. 

(Doc current as of 10-10-2024.)