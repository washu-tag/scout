<#import "template.ftl" as layout>
<@layout.emailLayout>
${kcSanitize(msg("emailAdminApprovalBodyHtml", username, scoutUrl))?no_esc}
</@layout.emailLayout>