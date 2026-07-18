# Contributing to Sentinel

First off, thank you for considering contributing to Sentinel! It's people like you that make Sentinel such a powerful guardrail for AI coding assistants.

Following these guidelines helps to communicate that you respect the time of the developers managing and developing this open source project. In return, they should reciprocate that respect in addressing your issue, assessing changes, and helping you finalize your pull requests.

## Development Environment Setup

Since Sentinel runs locally on your machine, you'll need to set up the local environment to test your changes.

1. **Fork the repository** to your own GitHub account.
2. **Clone the project** to your local machine:
   ```bash
   git clone https://github.com/YOUR-USERNAME/sentinel-mcp.git
   cd sentinel-mcp
   ```
3. **Run the local setup script** (Windows):
   ```bash
   start.bat
   ```
   *This will automatically create a virtual environment, install dependencies, train the classifier, and launch the API and Dashboard.*
4. **Alternative manual setup** (Mac/Linux):
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python train/train_classifier.py
   ```

## Ground Rules

- Ensure cross-platform compatibility for every change that's accepted. Windows, Mac, Debian & Ubuntu Linux.
- Ensure that code that goes into core meets all requirements in this checklist.
- Create issues for any major changes and enhancements that you wish to make. Discuss things transparently and get community feedback.
- Keep feature requests small and isolated. If you are adding a completely new UI or an entirely new ML model, discuss it in an issue first.

## Pull Request Lifecycle

1. Create a new branch for your feature/bugfix: `git checkout -b feature/your-feature-name`
2. Make your changes and test them locally. Ensure the API dashboard and MCP servers (`server.py`, `sse_server.py`) still boot correctly.
3. Commit your changes with descriptive commit messages.
4. Push to your fork: `git push origin feature/your-feature-name`
5. Open a Pull Request against the `main` branch. 
6. Fill out the Pull Request template completely. Ensure all checklist items are ticked.

## Releasing

Sentinel uses semantic versioning. Releases are performed by tagging the `main` branch and creating a GitHub Release.
Maintainers will bundle a ZIP of the source code with the release. 
Since this is a local-host server, users update by either pulling the latest changes from `main` or downloading the latest Release ZIP.
