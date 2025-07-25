<#import "template.ftl" as layout>
<@layout.emailLayout>
${kcSanitize(msg("emailAdminApprovalBodyHtml", username))?no_esc}
</@layout.emailLayout>