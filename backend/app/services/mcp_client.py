from __future__ import annotations

import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run_compliance_check(
    ifc_file_path: str,
    spec_file_path: str,
    mcp_server_path: str,
) -> dict:
    """Spawn VeritasMCP and run the 3-tool compliance pipeline.

    Args:
        ifc_file_path:   Absolute path to the .ifc file on disk.
        spec_file_path:  Absolute path to the spec .pdf file on disk.
        mcp_server_path: Absolute path to VeritasMCP's built index.js.

    Returns:
        The raw ComplianceReport dict from VeritasMCP's check_compliance tool.

    Raises:
        RuntimeError: If VeritasMCP returns an error response on any tool call.
        FileNotFoundError: If mcp_server_path does not exist.
    """
    params = StdioServerParameters(command="node", args=[mcp_server_path])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            load_result = await session.call_tool(
                "load_ifc", {"filePath": ifc_file_path}
            )
            if load_result.isError:
                raise RuntimeError(
                    f"VeritasMCP load_ifc failed: {load_result.content[0].text}"
                )

            parse_result = await session.call_tool(
                "parse_spec", {"filePath": spec_file_path}
            )
            if parse_result.isError:
                raise RuntimeError(
                    f"VeritasMCP parse_spec failed: {parse_result.content[0].text}"
                )

            check_result = await session.call_tool(
                "check_compliance",
                {"modelPath": ifc_file_path, "specPath": spec_file_path},
            )
            if check_result.isError:
                raise RuntimeError(
                    f"VeritasMCP check_compliance failed: {check_result.content[0].text}"
                )

            return json.loads(check_result.content[0].text)
