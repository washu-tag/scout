<#import "template.ftl" as layout>
<@layout.emailLayout>
${kcSanitize(msg("emailUserPendingBodyHtml"))?no_esc}
</@layout.emailLayout>