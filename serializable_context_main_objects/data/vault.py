

class VaultSecretReference(BaseModel):
    vaultIdentity: IdentityReference
    location: VaultPath
    attributes: list[str]
