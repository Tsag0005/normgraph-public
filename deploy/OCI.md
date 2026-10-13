# OCI Always Free deployment

This guide starts when you have an Oracle Cloud account. The site needs a VM
because graph building and OntoGPT extraction run in Python. The website and
API share one HTTPS hostname; no operator OpenAI or Neo4j credentials are used.

## 1. Create the VM

In your tenancy's home region, create an **Always Free eligible** Ubuntu
instance with the **VM.Standard.A1.Flex** shape. Allocate **2 OCPUs and 12 GB
RAM** if available. Use a public subnet, assign a public IPv4 address, and
save the SSH private key. Oracle's [instance walkthrough](https://docs.oracle.com/en-us/iaas/Content/Compute/tutorials/first-linux-instance/overview.htm)
covers the account, network, and SSH steps. A1 capacity is sometimes unavailable;
try another availability domain or later if Oracle reports insufficient capacity.

In the VM's network security group or subnet security list, allow inbound TCP
**80** and **443** from the internet. Keep SSH (**22**) restricted to your own
IP. Also permit 80 and 443 in the VM's host firewall if one is enabled.

## 2. Install Docker and get the code

Connect over SSH. Follow Docker's current [Ubuntu Engine and Compose plugin
instructions](https://docs.docker.com/engine/install/ubuntu/) and confirm
`sudo docker compose version` works. Clone this repository to the VM.

If the GitHub repository is private, give the VM a **read-only GitHub deploy
key** before cloning. On the VM, generate a key and show its public half:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/normgraph_deploy -N ''
cat ~/.ssh/normgraph_deploy.pub
```

Add that public key under the repository's **Settings → Deploy keys**, then
clone using the private key:

```bash
GIT_SSH_COMMAND='ssh -i ~/.ssh/normgraph_deploy -o IdentitiesOnly=yes' \
  git clone git@github.com:Tsag0005/normgraph-public.git
cd normgraph-public
```

## 3. Choose a free HTTPS hostname

[`sslip.io`](https://nip.io/) resolves a hostname containing a public IP to
that IP; registration is unnecessary. For example, a VM at `203.0.113.10`
would use `203.0.113.10.sslip.io`. Replace the sample address with your
instance's **actual public IPv4**:

```bash
printf 'SITE_ADDRESS=%s.sslip.io\n' 'YOUR_PUBLIC_IP' > .env
sudo docker compose up --build -d
sudo docker compose ps
sudo docker compose logs --tail=100 web api
```

Open `https://YOUR_PUBLIC_IP.sslip.io` and check that it uses a valid HTTPS
certificate. Caddy requests and renews the certificate automatically when the
hostname resolves and ports 80 and 443 are reachable. Replace the sslip.io
hostname later with your own DNS name if desired.

The first Docker build downloads two Python environments and can take time.
The API's visitor working files use a RAM-backed temporary filesystem and do
not survive a container restart. Each browser restores its latest IndexedDB
workspace snapshot after reconnecting. Visitors should export important
workspaces, especially before changing hostnames or clearing browser data.

## 4. Updates

From the VM's repository directory:

```bash
git pull --ff-only
sudo docker compose up --build -d
```

Keep the VM and Docker packages updated. Use `sudo docker compose logs -f web
api` when diagnosing deployment failures. If the VM's public IP changes, update
`SITE_ADDRESS` and open the new hostname; the old browser origin's IndexedDB
does not automatically transfer. Export a workspace ZIP from the old hostname
first, then import it on the new one.

Oracle documents the [Always Free limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
including the A1 monthly allowance and possible idle-instance reclamation.
