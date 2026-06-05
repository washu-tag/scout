<#ftl output_format="plainText">
User ${username} has requested access to Scout<#if scoutSite??> (${scoutSite})</#if>.

<#if approvalUrl??>Review and approve their request:
${approvalUrl}
<#else>Please log in to Scout ( ${scoutUrl} ) to enable their account.</#if>