# Scout XNAT POC 2026-04-30

This is the human-to-human manually written doc summarizing the
progress of my POC for the week of 2026-04-27. There are lots of
Claude-written docs in this branch as well with more details about
what we were doing in this POC, progress notes, and next steps.

## POC Aims
The aim of this POC was testing the auth story for an XNAT in Scout.

- Start with an existing Scout
- Deploy a fresh XNAT into it
- Integrate XNAT's auth into Scout's Keycloak

The intended demoable goal was to start a Jupyter notebook (or a playbook) which could
auth to the Scout XNAT.

## POC Status

I was able to get this all hooked up on the [dev03 cluster](https://dev03.tag.rcif.io).

1. Log in to Scout with GitHub
2. Separately log in to Keycloak and add yourself to the `scout-user` group
3. Navigate to XNAT: https://xnat.dev03.tag.rcif.io
4. Click "Sign in with Scout"

## Deployment Details

The Scout was deployed using the existing ansible. I made some tweaks to the 
Keycloak realm config to add XNAT-relevant things to the Scout realm.

I used the [NrgXnat helm charts](https://github.com/NrgXnat/helm-charts) to deploy XNAT
to that Scout cluster. The chart worked mostly as-is, with a few small workarounds.

To set up the auth, I used the [XNAT openid-auth-plugin](https://bitbucket.org/xnatx/openid-auth-plugin)
(a.k.a. OIDC Plugin) configured to point to Keycloak. Using this plugin was an assumption 
I brought in to the POC which had a lot of knock-on effects in how the auth worked 
and what we were able to do.

## Auth Story

At a basic level, the auth worked as expected.
I could create a user in Scout and assign them to the `scout-users` group, and that
was sufficient for them to log in to XNAT. I also tested the negative case; a user that
existed in Keycloak but hadn't been added to `scout-users` could not log in to XNAT.

But I didn't have a good way to do the deeper integration: XNAT API access from elsewhere in Scout.
The issue is how to auth. Since we used the OIDC plugin, which is fundamentally browser based, 
a user in a notebook doesn't have anything they can pass to an XNAT API. They could 
log in to XNAT in another tab and manually grab an alias token, but that's pretty gross.

## Next Steps
I think the decision to use the OIDC Plugin presented a fundamental limitation in what we were
able to do. I propose that we could do a follow-up POC where we whip up a small new 
token-based auth plugin. This would let us put XNAT behind OA2P, similar to all the other
applications in Scout. And it would let us add a token-exchange mechanism so Jupyter users
could auth to XNAT with their Jupyter Keycloak token, and our plugin would be able to 
talk to Keycloak and exchange that for an XNAT token.

## Docs
If you want to go deep on this POC, here's a summary of the Claude-written docs in this branch:

- [xnat-deployment-plan.md](../docs/internal/xnat-deployment-plan.md) and [xnat-deployment-runbook.md](../docs/internal/xnat-deployment-runbook.md) - These drove development and implementation of the POC
- [xnat-k3d-runbook.md](../docs/internal/xnat-k3s-runbook.md) - When I couldn't deploy to `dev03` and had to pivot to trying a local k3d install, this drove the deployment of the POC
- [xnat-k3d-progress.md](../docs/internal/xnat-k3d-progress.md) - Ongoing progress doc for weird things that were found / questions that came up during the k3d deploy
- [xnat-dev-review.md](../docs/internal/xnat-dev-review.md) - For this doc I wanted to reorient myself, because I realized the POC that I had built was good but didn't necessarily align with where I thought I was going on Monday. So I asked Claude a bunch of questions about what the POC was doing and how it related to the aims, as well as questions about what we did because it was necessary and what we did just because it was a first pass. It answered those questions (though didn't reproduce them, which is a bit annoying) and presented some ideas about next steps.
- [xnat-auth-poc.md](../docs/internal/xnat-auth-poc.md) - This is the big, main doc that summarizes everything that we did, everything we learned, and what I think we should do next.

(Note that while each of these was written by Claude, I was reading, reviewing, and revising along the way.)
