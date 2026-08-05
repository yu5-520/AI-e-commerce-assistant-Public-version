# Verified precise release migration

Current repository state: `MIGRATION_CONTROLLER_READY`.

The target repository must remain private. Place the verified parent release bundle at:

```
migration/release-bundle.tar.gz
```

Expected identities:

- Source repository: `yu5-520/AI-e-commerce-assistant`
- Source commit: `f5186451c80631fea550da17d481f5e8793215e5`
- Artifact ID: `8815984381`
- Artifact digest: `sha256:b2c9892f3bcf066fabebe52c97aa6acd0b49c0316c40d2c909c0917bbb13f66f`
- Bundle SHA256: `8c46ca37519698399d0aaf93c1c970cff2472876b208f5ac561c5c3a4d6fa438`
- Parent release hash: `sha256:593b94a045c0532738ff2da0ed18ccd44179d425fac08cd8542203a644bc4d26`
- Parent manifest hash: `sha256:b019ceb2c1fff35e7ebb34ca5ebab4d13011e8eab95b32ff0e2bdb76a66cc6f3`

After the bundle is present, run the manual workflow `Import Verified Precise Release` on the repository-level self-hosted runner carrying the `public-prune` label.

The importer will:

1. verify that the repository is private;
2. verify the bundle SHA256;
3. safely extract the package;
4. run the package's own release verifier;
5. quarantine inherited mother-repository workflows;
6. preserve the parent release lineage;
7. upload files through the Git Data API with per-file progress;
8. create one final imported commit;
9. keep `publicationAllowed=false` until pruning and public-safety review finish.
