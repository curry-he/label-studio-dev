import pachyderm_sdk
from pachyderm_sdk.api import pfs, pps

def get_pach_client():
    """Initializes and returns a Pachyderm client."""
    return pachyderm_sdk.Client()

def create_repo(client: pachyderm_sdk.Client, repo_name: str):
    """Creates a new repo in Pachyderm if it doesn't exist."""
    try:
        client.pfs.create_repo(repo=pfs.Repo(name=repo_name))
        print(f"Repo '{repo_name}' created.")
    except Exception as e:
        if "already exists" in str(e):
            print(f"Repo '{repo_name}' already exists.")
        else:
            raise

def commit_file(client: pachyderm_sdk.Client, repo_name: str, file_path: str, file_content: bytes):
    """Commits a single file to a Pachyderm repo."""
    with client.pfs.commit(branch=pfs.Branch.from_uri(f"{repo_name}@master")) as commit:
        commit.put_file_from_bytes(path=file_path, data=file_content)

def create_pipeline(client: pachyderm_sdk.Client, pipeline_name: str, image: str, cmd: list, input_repo: str, output_repo: str):
    """Creates a new pipeline in Pachyderm."""
    client.pps.create_pipeline(
        pipeline=pps.Pipeline(name=pipeline_name),
        transform=pps.Transform(image=image, cmd=cmd),
        input=pps.Input(pfs=pps.PFSInput(repo=input_repo, glob="/*")),
        output_branch=pfs.Branch(repo=pfs.Repo(name=output_repo), name="master"),
        update=True,
    )
    print(f"Pipeline '{pipeline_name}' created/updated.")
